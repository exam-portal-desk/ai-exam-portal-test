"""
app/routes/web/chat.py
Chat page shell. The JSON/AJAX API and SocketIO events that used to live
alongside this in app/routes/chat.py now live in
app/routes/api/v01/chat.py, backed by app/services/chat_service.py and
app/db/chat.py.
"""

from flask import Blueprint, session, render_template, redirect, url_for

from app.db.users import get_user_by_id
from app.services.image_storage_service import chat_background_url_from_key

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/chat')
def chat_page():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))

    user = get_user_by_id(int(session['user_id'])) or {}
    chat_bg_type = user.get('chat_background_type')
    chat_bg_value = user.get('chat_background_value')
    chat_bg_key = user.get('chat_background_key')
    chat_bg_zoom = user.get('chat_background_zoom') if user.get('chat_background_zoom') is not None else 1.0
    chat_bg_pos_x = user.get('chat_background_pos_x') if user.get('chat_background_pos_x') is not None else 0.5
    chat_bg_pos_y = user.get('chat_background_pos_y') if user.get('chat_background_pos_y') is not None else 0.5
    chat_bg_overlay = user.get('chat_background_overlay') if user.get('chat_background_overlay') is not None else 0.45
    # Resolved whenever a custom image is saved, even if a preset is
    # currently active — the picker needs this to offer "bring back your
    # saved image" without a round-trip.
    chat_bg_custom_url = chat_background_url_from_key(chat_bg_key) if chat_bg_key else None

    return render_template(
        'chat.html',
        chat_bg_type=chat_bg_type, chat_bg_value=chat_bg_value,
        chat_bg_zoom=chat_bg_zoom, chat_bg_pos_x=chat_bg_pos_x,
        chat_bg_pos_y=chat_bg_pos_y, chat_bg_overlay=chat_bg_overlay,
        chat_bg_has_custom=bool(chat_bg_key), chat_bg_custom_url=chat_bg_custom_url,
    )
