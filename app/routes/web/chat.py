"""
app/routes/web/chat.py
Chat page shell. The JSON/AJAX API and SocketIO events that used to live
alongside this in app/routes/chat.py now live in
app/routes/api/v01/chat.py, backed by app/services/chat_service.py and
app/db/chat.py.
"""

from flask import Blueprint, session, render_template, redirect, url_for

chat_bp = Blueprint('chat', __name__)


@chat_bp.route('/chat')
def chat_page():
    if not session.get('user_id'):
        return redirect(url_for('auth.login'))
    return render_template('chat.html')
