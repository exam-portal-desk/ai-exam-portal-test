"""
app/routes/api/v01/discussions.py
Question discussion/comment JSON API (v01) + SocketIO realtime events.
Relocated from app/routes/discussion.py. All raw SQL moved to
app/db/discussion.py and all business logic (rate limiting, thread
building, ghost-account redaction, count-cache sync) moved to
app/services/discussion_service.py — this file is now just request
parsing, auth/ownership checks, response shaping, and socket emits.

Fixes applied while relocating (see discussion_service.py for detail):
  - Comment-count sync is now a single atomic UPSERT instead of a racy
    SELECT-then-UPDATE/INSERT.
  - Dead message-buffering thread (declared, started, never fed) removed.
  - 'leave_discussion' socket handler now requires an authenticated
    session, matching 'join_discussion' (previously the only handler in
    this file with no auth check at all).

Endpoints:
  GET    /api/v01/discussions/<question_id>                       — full thread
  POST   /api/v01/discussions/<question_id>                       — post a comment
  PUT    /api/v01/discussions/comments/<comment_id>                — edit a comment
  DELETE /api/v01/discussions/comments/<comment_id>                — delete a comment
  POST   /api/v01/discussions/counts                                — bulk comment counts
  PUT    /api/v01/admin/discussions/comments/<comment_id>/pin       — toggle pin (admin)
  PUT    /api/v01/admin/discussions/comments/<comment_id>/best      — toggle best answer (admin)
"""

from flask import Blueprint, request, jsonify, session
from flask_socketio import join_room, leave_room

import threading
import app.services.discussion_service as discussion_service

discussion_bp = Blueprint('discussion', __name__, url_prefix='/api/v01/discussions')
discussion_admin_bp = Blueprint('discussion_admin_api', __name__, url_prefix='/api/v01/admin/discussions')

socketio = None


def init_socketio(sio):
    global socketio
    socketio = sio


def _is_admin():
    return 'admin' in str(session.get('role', ''))


@discussion_bp.route('/<int:question_id>', methods=['GET'])
def get_discussion(question_id):
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    try:
        thread = discussion_service.get_discussion_thread(question_id, session['user_id'])
        return jsonify({'success': True, 'comments': thread['comments'], 'count': thread['count']})
    except Exception as e:
        print(f"[Disc] GET error: {e}")
        return jsonify({'success': False}), 500


@discussion_bp.route('/<int:question_id>', methods=['POST'])
def post_comment(question_id):
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    uid = session['user_id']
    if not discussion_service.rate_ok(uid):
        return jsonify({'success': False,
                        'message': f'Wait {discussion_service.RATE_LIMIT_SECONDS}s before posting again.'}), 429

    data = request.get_json() or {}
    username = session.get('full_name') or session.get('username', 'User')

    try:
        err, status, payload = discussion_service.create_comment(question_id, uid, username, data)
    except Exception as e:
        print(f"[Disc] insert error: {e}")
        return jsonify({'success': False, 'message': 'Failed to save message'}), 500

    if err:
        return jsonify({'success': False, 'message': err}), status

    broadcast_msg = {**payload, 'is_own': False}
    if socketio:
        threading.Thread(
            target=lambda: socketio.emit('new_message', broadcast_msg, room=f'q_{question_id}'),
            daemon=True
        ).start()

    own_msg = {**payload, 'is_own': True}
    return jsonify({'success': True, 'message': own_msg})


@discussion_bp.route('/comments/<int:comment_id>', methods=['PUT', 'DELETE'])
def comment_api(comment_id):
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    uid = session['user_id']

    if request.method == 'PUT':
        data = request.get_json() or {}
        try:
            err, status, result = discussion_service.edit_comment(comment_id, uid, _is_admin(), data.get('message', ''))
        except Exception:
            return jsonify({'success': False}), 500
        if err:
            return jsonify({'success': False, 'message': err}), status
        if socketio:
            socketio.emit('edit_message', {'comment_id': comment_id, 'message': result['message']},
                          room=f'q_{result["question_id"]}')
        return jsonify({'success': True})

    # DELETE
    try:
        err, status, qid = discussion_service.delete_comment(comment_id, uid, _is_admin())
    except Exception:
        return jsonify({'success': False}), 500
    if err:
        return jsonify({'success': False}), status
    if socketio:
        socketio.emit('delete_message', {'comment_id': comment_id}, room=f'q_{qid}')
    return jsonify({'success': True})


@discussion_bp.route('/counts', methods=['POST'])
def bulk_counts():
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    qids = data.get('question_ids', [])
    if not qids or len(qids) > 100:
        return jsonify({'success': False}), 400
    try:
        return jsonify({'success': True, 'counts': discussion_service.bulk_counts(qids)})
    except Exception as e:
        print(f"[Disc] bulk counts error: {e}")
        return jsonify({'success': False}), 500


# ─────────────────────────────────────────────
# Admin-only actions
# ─────────────────────────────────────────────

@discussion_admin_bp.route('/comments/<int:comment_id>/pin', methods=['PUT'])
def admin_pin(comment_id):
    if not _is_admin():
        return jsonify({'success': False}), 403
    try:
        qid, new_val = discussion_service.toggle_pin(comment_id)
    except Exception:
        return jsonify({'success': False}), 500
    if qid is None:
        return jsonify({'success': False}), 404
    if socketio:
        socketio.emit('pin_message', {'comment_id': comment_id, 'is_pinned': new_val}, room=f'q_{qid}')
    return jsonify({'success': True, 'is_pinned': new_val})


@discussion_admin_bp.route('/comments/<int:comment_id>/best', methods=['PUT'])
def admin_best(comment_id):
    if not _is_admin():
        return jsonify({'success': False}), 403
    try:
        qid, new_val = discussion_service.toggle_best(comment_id)
    except Exception:
        return jsonify({'success': False}), 500
    if qid is None:
        return jsonify({'success': False}), 404
    if socketio:
        socketio.emit('best_message', {'comment_id': comment_id, 'is_best_answer': new_val}, room=f'q_{qid}')
    return jsonify({'success': True, 'is_best_answer': new_val})


# ─────────────────────────────────────────────
# SocketIO events
# ─────────────────────────────────────────────

def register_socketio_events(sio):
    @sio.on('join_discussion')
    def on_join(data):
        if 'user_id' not in session:
            return
        qid = data.get('question_id')
        if qid:
            join_room(f'q_{qid}')

    @sio.on('leave_discussion')
    def on_leave(data):
        if 'user_id' not in session:
            return
        qid = data.get('question_id')
        if qid:
            leave_room(f'q_{qid}')
