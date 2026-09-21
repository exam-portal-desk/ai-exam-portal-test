"""
app/routes/api/v01/plan.py
The signed-in student's plan, for the front-end. It is the same view the server enforces and the pages render
(app.entitlements.user_plan); nothing on the client decides or stores a plan.

  GET /api/v01/me/plan   -> {"plan", "plan_label", "tone", "icon", "expires_at", "custom", "features": [{"key", "label", "allowed", ...}]}
"""

from flask import Blueprint, jsonify, session

from app import entitlements
from app.middleware.session_guard import require_user_role

plan_api_bp = Blueprint("plan_api", __name__, url_prefix="/api/v01/me")


@plan_api_bp.route("/plan")
@require_user_role
def api_my_plan():
    return jsonify({"success": True, **entitlements.user_plan(int(session["user_id"]))})
