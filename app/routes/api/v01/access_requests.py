"""
app/routes/api/v01/access_requests.py
Access-request JSON API (v01) — a logged-out user checking/requesting a role
change (e.g. student wanting admin access). Relocated from the two JSON
routes that used to live inline inside app/routes/auth.py; logic unchanged.

Endpoints:
  POST /api/v01/access-requests/validate-user  — look up a user + their request history
  POST /api/v01/access-requests                — submit a new access request
"""

from flask import Blueprint, request, jsonify

from app.db.users import get_user_by_username
from app.utils.datetime_service import now_utc_naive, format_display

access_requests_bp = Blueprint("access_requests_api", __name__, url_prefix="/api/v01/access-requests")


@access_requests_bp.route("/validate-user", methods=["POST"])
def api_validate_user_for_request():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip().lower()

    if not username or not email:
        return jsonify({"success": False, "message": "Username and email are required"}), 400

    user = get_user_by_username(username)
    if not user or str(user.get("email","")).lower() != email:
        return jsonify({"success": False, "message": "User not found"}), 404

    from app.db.misc import get_requests_by_user, available_role_requests, split_request_reason
    current_access = str(user.get("role","user")).strip().lower()
    reqs = get_requests_by_user(username, email)

    formatted = [
        {"request_id": int(r.get("request_id",0)),
         "requested_access": r.get("requested_access",""),
         "request_date": format_display(r.get("request_date")),
         "status": r.get("request_status",""),
         "reason": split_request_reason(r.get("reason",""))[0]}
        for r in reqs
    ]

    # Same one rule profile.py's own request card uses (app/db/misc.py) — a "user" account may
    # ask for admin-only or both; a dual-role account may only ask to drop back to user-only
    # (admin-only never reaches this logged-out form at all, since it can't sign in to it).
    available = available_role_requests(current_access)

    has_pending = any(r["status"] == "pending" for r in formatted)

    return jsonify({
        "success": True,
        "user": {"username": username, "email": email,
                 "current_access": current_access,
                 "full_name": user.get("full_name", username)},
        "requests": formatted,
        "available_requests": available,
        "has_pending_request": has_pending,
        # False only for an admin-only account (never reaches the user portal to ask from there,
        # but this logged-out form takes any username+email pair) — a dual-role account now
        # always has at least the downgrade-to-user option, so it's never "nothing to do" for it.
        "can_request": bool(available) and not has_pending,
    })


@access_requests_bp.route("", methods=["POST"])
def api_submit_access_request():
    data = request.get_json() or {}
    required = ["username","email","current_access","requested_access"]
    for f in required:
        if not data.get(f):
            return jsonify({"success": False, "message": f"{f} is required"}), 400

    username         = data["username"].strip()
    email            = data["email"].strip().lower()
    current_access   = data["current_access"].strip().lower()
    requested_access = data["requested_access"].strip().lower()
    user_reason      = data.get("user_reason","").strip()

    if not user_reason:
        return jsonify({"success": False, "message": "Please provide a reason"}), 400

    from app.db.misc import get_requests_by_user, create_request
    pending = [r for r in get_requests_by_user(username, email) if r.get("request_status") == "pending"]
    if pending:
        return jsonify({"success": False, "message": "You already have a pending request"}), 400

    new_req = {
        "username": username, "email": email,
        "current_access": current_access, "requested_access": requested_access,
        "request_date": now_utc_naive().isoformat(),
        "request_status": "pending",
        "reason": f"[USER REQUEST] {user_reason}",
    }
    created = create_request(new_req)
    if not created:
        return jsonify({"success": False, "message": "Failed to save request"}), 500

    return jsonify({"success": True,
                    "message": "Request submitted. Please wait for admin approval.",
                    "request_id": int(created.get("request_id",0))})
