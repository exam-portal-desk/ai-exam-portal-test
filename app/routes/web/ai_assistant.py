"""
app/routes/web/ai_assistant.py
AI Study Assistant page. JSON API lives in app/routes/api/v01/assistant.py.
"""

from flask import Blueprint, render_template, session

from app.middleware.session_guard import require_user_role

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/ai-assistant")
@require_user_role
def ai_assistant():
    return render_template(
        "ai_assistant.html",
        username=session.get("username"),
        full_name=session.get("full_name"),
    )
