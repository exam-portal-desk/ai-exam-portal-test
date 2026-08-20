"""
app/routes/api/v01/chat.py
Chat JSON API (v01) + SocketIO realtime events. Relocated from
app/routes/chat.py. All raw SQL moved to app/db/chat.py and all business
logic (rate limiting, member/online caches, unread buffering, DM lookup,
message normalization) moved to app/services/chat_service.py — this file
is now request parsing, membership/ownership checks, response shaping,
and socket emits.

Fix applied while relocating (see chat_service.get_conversations_for_user
and app/db/chat.py for detail): the conversation list's last-message
lookup now runs as one query for all conversations instead of one query
per conversation (flagged as a High-severity N+1 in the architecture
audit), and get_messages' membership check + joined_at fetch — previously
two near-identical queries — are now a single query.

Endpoints:
  GET    /api/v01/chat/users/search
  POST   /api/v01/chat/friend-requests
  PUT    /api/v01/chat/friend-requests/<conn_id>
  GET    /api/v01/chat/friend-requests/inbox
  GET    /api/v01/chat/conversations
  GET    /api/v01/chat/conversations/<conv_id>/messages
  POST   /api/v01/chat/conversations/<conv_id>/messages
  PUT    /api/v01/chat/messages/<msg_id>
  DELETE /api/v01/chat/messages/<msg_id>
  DELETE /api/v01/chat/conversations/<conv_id>/clear
  POST   /api/v01/chat/conversations/<conv_id>/read
  DELETE /api/v01/chat/friends/<other_id>
  DELETE /api/v01/chat/groups/<conv_id>/exit
  DELETE /api/v01/chat/groups/<conv_id>
  POST   /api/v01/chat/groups
  POST   /api/v01/chat/groups/<conv_id>/members
  DELETE /api/v01/chat/groups/<conv_id>/members/<target_uid>
  GET    /api/v01/chat/unread-count
  GET    /api/v01/chat/online-status
"""

from flask import Blueprint, request, jsonify, session
from flask_socketio import join_room, leave_room

import app.db.chat as chat_db
import app.services.chat_service as chat_service
from app.utils.datetime_service import now_utc_naive
from app.services.image_storage_service import profile_photo_url_from_key

chat_api_bp = Blueprint('chat_api', __name__, url_prefix='/api/v01/chat')
socketio = None


def init_chat_socketio(sio):
    global socketio
    socketio = sio


def _uid():
    return session.get('user_id')


def _uname():
    return session.get('full_name') or session.get('username', 'User')


def _emit(event, data, room, skip_uid=None):
    if not socketio:
        return
    skip_sid = chat_service.get_sid_for(skip_uid) if skip_uid else None
    try:
        socketio.emit(event, data, room=room, skip_sid=skip_sid)
    except Exception:
        socketio.emit(event, data, room=room)


@chat_api_bp.route('/users/search')
def search_users():
    if not _uid():
        return jsonify({'success': False}), 401
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'success': True, 'users': []})
    try:
        res = chat_db.search_users(q, _uid())
        if not res:
            return jsonify({'success': True, 'users': []})
        user_ids = [u['id'] for u in res]
        conns = chat_db.get_connections_for_users(_uid(), user_ids)
        conn_map = {}
        for c in conns:
            other = c['recipient_id'] if c['requester_id'] == _uid() else c['requester_id']
            conn_map[other] = c['status']
        users = [{'id': u['id'], 'username': u['username'], 'full_name': u['full_name'],
                  'photo_url': profile_photo_url_from_key(u.get('profile_photo_key')),
                  'online': chat_service.is_online(u['id']), 'connection_status': conn_map.get(u['id'])} for u in res]
        return jsonify({'success': True, 'users': users})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/friend-requests', methods=['POST'])
def send_request():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    rid = data.get('recipient_id')
    if not rid or rid == _uid():
        return jsonify({'success': False}), 400
    try:
        row = chat_db.get_connection_between(_uid(), rid)
        if row:
            if row['status'] == 'pending':
                return jsonify({'success': False, 'message': 'Request already sent'}), 409
            elif row['status'] == 'accepted':
                return jsonify({'success': False, 'message': 'Already connected'}), 409
            elif row['status'] == 'rejected':
                chat_db.reactivate_connection(row['id'], _uid(), rid, now_utc_naive().isoformat())
                if socketio:
                    socketio.emit('chat_request', {'from_id': _uid(), 'from_name': _uname()}, room=f'user_{rid}')
                return jsonify({'success': True})
        chat_db.create_connection(_uid(), rid)
        if socketio:
            socketio.emit('chat_request', {'from_id': _uid(), 'from_name': _uname()}, room=f'user_{rid}')
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/friend-requests/<int:conn_id>', methods=['PUT'])
def respond_request(conn_id):
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    action = data.get('action')
    if action not in ('accept', 'reject'):
        return jsonify({'success': False}), 400
    try:
        row = chat_db.get_connection_by_id(conn_id)
        if not row or row['recipient_id'] != _uid():
            return jsonify({'success': False}), 403
        status = 'accepted' if action == 'accept' else 'rejected'
        chat_db.update_connection_status(conn_id, status, now_utc_naive().isoformat())
        req_id = row['requester_id']
        if action == 'accept':
            conv_id = chat_service.get_or_create_dm_conv(_uid(), req_id)
            if socketio:
                socketio.emit('request_accepted', {'by_name': _uname(), 'conv_id': conv_id}, room=f'user_{req_id}')
            return jsonify({'success': True, 'conv_id': conv_id})
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/friend-requests/inbox')
def inbox():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        res = chat_db.get_pending_requests_for(_uid())
        if not res:
            return jsonify({'success': True, 'requests': []})
        req_ids = [r['requester_id'] for r in res]
        from app.db.users import get_users_by_ids
        # get_users_by_ids() returns a dict keyed by str(id) — convert to int keys
        # since requester_id (used for the .get() lookups below) is an int.
        users_res = get_users_by_ids(req_ids)
        user_map = {int(uid_str): u for uid_str, u in users_res.items()}
        requests = [{'conn_id': r['id'], 'from_id': r['requester_id'],
                     'from_name': user_map.get(r['requester_id'], {}).get('full_name') or user_map.get(r['requester_id'], {}).get('username') or 'User',
                     'photo_url': profile_photo_url_from_key(user_map.get(r['requester_id'], {}).get('profile_photo_key')),
                     'created_at': r['created_at']} for r in res]
        return jsonify({'success': True, 'requests': requests})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/conversations')
def get_conversations():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        result = chat_service.get_conversations_for_user(_uid())
        return jsonify({'success': True, 'conversations': result})
    except Exception as e:
        print(f'[Chat] convs error: {e}')
        return jsonify({'success': False}), 500


@chat_api_bp.route('/conversations/<int:conv_id>/messages')
def get_messages(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    uid = _uid()
    membership = chat_db.get_membership(conv_id, uid)
    if not membership:
        return jsonify({'success': False}), 403
    try:
        cleared_at = membership.get('joined_at')
        before = request.args.get('before')

        res = chat_db.get_messages(conv_id, before, cleared_at)
        msgs = list(reversed(res))
        msgs = [chat_service.normalize_message(m, uid) for m in msgs]

        chat_db.reset_unread(uid, conv_id)
        return jsonify({'success': True, 'messages': msgs})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/conversations/<int:conv_id>/messages', methods=['POST'])
def send_message(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    uid = _uid()
    if not chat_service.rate_ok(uid):
        return jsonify({'success': False, 'message': 'Slow down'}), 429
    if not chat_db.get_membership(conv_id, uid):
        return jsonify({'success': False}), 403
    data = request.get_json() or {}
    msg = data.get('message', '').strip()
    if not msg or len(msg) > chat_service.MAX_CHAT_MSG_LEN:
        return jsonify({'success': False}), 400
    msg = chat_service.sanitize(msg)
    name = _uname()
    now = now_utc_naive().isoformat()
    reply_to_id = data.get('reply_to_id')
    reply_to_text = data.get('reply_to_text', '')[:100] if data.get('reply_to_text') else None
    reply_to_name = data.get('reply_to_name') or None
    record = {
        'conversation_id': conv_id, 'sender_id': uid, 'sender_name': name,
        'message': msg, 'created_at': now,
        'reply_to_id': reply_to_id, 'reply_to_text': reply_to_text, 'reply_to_name': reply_to_name
    }
    try:
        res = chat_db.insert_message(record)
        msg_id = res['id']
        chat_service.buffer_unread(conv_id, uid)
        broadcast = {
            'id': msg_id, 'sender_name': name, 'message': msg, 'created_at': now,
            'is_own': False, 'conv_id': conv_id,
            'is_deleted_account': False,
            'reply_to_id': reply_to_id, 'reply_to_text': reply_to_text, 'reply_to_name': reply_to_name
        }
        _emit('chat_message', broadcast, room=f'conv_{conv_id}', skip_uid=uid)
        return jsonify({'success': True, 'message': {**broadcast, 'is_own': True}})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/messages/<int:msg_id>', methods=['PUT', 'DELETE'])
def message_api(msg_id):
    if not _uid():
        return jsonify({'success': False}), 401

    if request.method == 'PUT':
        data = request.get_json() or {}
        new_text = data.get('message', '').strip()
        if not new_text or len(new_text) > chat_service.MAX_CHAT_MSG_LEN:
            return jsonify({'success': False, 'message': 'Invalid message'}), 400
        new_text = chat_service.sanitize(new_text)
        try:
            row = chat_db.get_message_owner(msg_id)
            if not row or row['sender_id'] != _uid():
                return jsonify({'success': False}), 403
            # Prevent editing a deleted-account placeholder
            if row.get('sender_name') == chat_service.DELETED_SENDER_NAME:
                return jsonify({'success': False, 'message': 'Cannot edit this message'}), 403
            conv_id = row['conversation_id']
            chat_db.update_message_text(msg_id, new_text)
            _emit('msg_edited', {'msg_id': msg_id, 'message': new_text, 'conv_id': conv_id}, room=f'conv_{conv_id}')
            return jsonify({'success': True, 'message': new_text})
        except Exception:
            return jsonify({'success': False}), 500

    # DELETE
    try:
        row = chat_db.get_message_owner(msg_id)
        if not row or row['sender_id'] != _uid():
            return jsonify({'success': False}), 403
        # Prevent deleting a deleted-account placeholder
        if row.get('sender_name') == chat_service.DELETED_SENDER_NAME:
            return jsonify({'success': False, 'message': 'Cannot delete this message'}), 403
        conv_id = row['conversation_id']
        chat_db.soft_delete_message(msg_id)
        _emit('msg_deleted', {'msg_id': msg_id, 'conv_id': conv_id}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/conversations/<int:conv_id>/clear', methods=['DELETE'])
def clear_chat(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    if not chat_db.get_membership(conv_id, _uid()):
        return jsonify({'success': False}), 403
    try:
        now = now_utc_naive().isoformat()
        chat_db.update_joined_at(conv_id, _uid(), now)
        chat_db.reset_unread(_uid(), conv_id)
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/conversations/<int:conv_id>/read', methods=['POST'])
def mark_read(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    if not chat_db.get_membership(conv_id, _uid()):
        return jsonify({'success': False}), 403
    try:
        chat_db.reset_unread(_uid(), conv_id)
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/friends/<int:other_id>', methods=['DELETE'])
def remove_friend(other_id):
    """
    Remove a friend connection and purge the shared DM conversation.
    Group chats are unaffected — only the DM between these two users is removed.
    """
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conn = chat_db.get_connection_between(_uid(), other_id)
        if conn:
            chat_db.delete_connection(conn['id'])

        conv_id = chat_service.find_dm_conversation_id_with(_uid(), other_id)

        if conv_id:
            chat_service.purge_conversation_fully(conv_id)
            if socketio:
                socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{other_id}')

        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] remove friend error: {e}')
        return jsonify({'success': False}), 500


@chat_api_bp.route('/groups/<int:conv_id>/exit', methods=['DELETE'])
def exit_group(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = chat_db.get_conversation(conv_id)
        if not conv:
            return jsonify({'success': False}), 404
        is_creator = conv['created_by'] == _uid()
        remaining = [m for m in chat_db.get_member_ids(conv_id) if m != _uid()]
        chat_db.remove_member(conv_id, _uid())
        chat_db.delete_unread(conv_id, _uid())
        chat_service.invalidate_member_cache(conv_id)
        if is_creator and remaining:
            chat_db.update_conversation_creator(conv_id, remaining[0])
        if socketio:
            socketio.emit('member_left', {'conv_id': conv_id, 'user_name': _uname()}, room=f'conv_{conv_id}')
        if not remaining:
            chat_db.delete_messages_and_conversation(conv_id)
        if socketio:
            socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{_uid()}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] exit group error: {e}')
        return jsonify({'success': False}), 500


@chat_api_bp.route('/groups/<int:conv_id>', methods=['DELETE'])
def delete_group(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = chat_db.get_conversation(conv_id)
        if not conv or conv['created_by'] != _uid():
            return jsonify({'success': False, 'message': 'Only group creator can delete the group'}), 403
        member_ids = chat_db.get_member_ids(conv_id)
        chat_service.invalidate_member_cache(conv_id)
        chat_db.delete_group_conversation(conv_id)
        if socketio:
            for m in member_ids:
                socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{m}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] delete group error: {e}')
        return jsonify({'success': False}), 500


@chat_api_bp.route('/groups', methods=['POST'])
def create_group():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    member_ids = data.get('member_ids', [])
    if not name or not member_ids:
        return jsonify({'success': False}), 400
    name = chat_service.sanitize(name)[:50]
    try:
        conv = chat_db.create_conversation(is_group=True, created_by=_uid(), group_name=name)
        cid = conv['id']
        all_members = list(set([_uid()] + member_ids))
        chat_db.add_members_bulk(cid, all_members)
        for m in all_members:
            if m != _uid() and socketio:
                socketio.emit('added_to_group', {'conv_id': cid, 'group_name': name}, room=f'user_{m}')
        return jsonify({'success': True, 'conv_id': cid})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/groups/<int:conv_id>/members', methods=['POST'])
def add_group_member(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    new_uid = data.get('user_id')
    if not new_uid:
        return jsonify({'success': False}), 400
    try:
        conv = chat_db.get_conversation(conv_id)
        if not conv or not conv.get('is_group'):
            return jsonify({'success': False, 'message': 'Group not found'}), 404
        if conv['created_by'] != _uid():
            role = chat_db.get_member_role(conv_id, _uid())
            if role != 'admin':
                return jsonify({'success': False, 'message': 'Only admins can add members'}), 403
        if chat_db.get_membership(conv_id, new_uid):
            return jsonify({'success': False, 'message': 'User already in group'}), 409
        chat_db.add_member(conv_id, new_uid)
        chat_service.invalidate_member_cache(conv_id)
        group_name = conv.get('group_name') or 'Group'
        if socketio:
            socketio.emit('added_to_group', {'conv_id': conv_id, 'group_name': group_name}, room=f'user_{new_uid}')
            socketio.emit('member_joined', {'conv_id': conv_id, 'user_id': new_uid}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] add member error: {e}')
        return jsonify({'success': False}), 500


@chat_api_bp.route('/groups/<int:conv_id>/members/<int:target_uid>', methods=['DELETE'])
def remove_group_member(conv_id, target_uid):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = chat_db.get_conversation(conv_id)
        if not conv:
            return jsonify({'success': False}), 404
        is_admin = conv['created_by'] == _uid()
        if not is_admin:
            role = chat_db.get_member_role(conv_id, _uid())
            is_admin = (role == 'admin')
        if not is_admin and target_uid != _uid():
            return jsonify({'success': False}), 403
        chat_db.remove_member(conv_id, target_uid)
        chat_db.delete_unread(conv_id, target_uid)
        chat_service.invalidate_member_cache(conv_id)
        if socketio:
            socketio.emit('removed_from_group', {'conv_id': conv_id}, room=f'user_{target_uid}')
            socketio.emit('member_left', {'conv_id': conv_id, 'user_id': target_uid}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False}), 500


@chat_api_bp.route('/unread-count')
def unread_count():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        unread_res = chat_db.get_unread_counts(_uid())
        total = sum(r['count'] for r in unread_res)
        pending_count = chat_db.count_pending_requests(_uid())
        return jsonify({'success': True, 'unread': total, 'requests': pending_count})
    except Exception:
        return jsonify({'success': True, 'unread': 0, 'requests': 0})


@chat_api_bp.route('/online-status')
def online_status():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.args.get('ids', '')
    try:
        ids = [int(x) for x in data.split(',') if x.strip()]
        result = {uid: chat_service.is_online(uid) for uid in ids}
        return jsonify({'success': True, 'status': result})
    except Exception:
        return jsonify({'success': False}), 400


def register_chat_socketio_events(sio):
    @sio.on('connect')
    def on_connect(auth=None):
        uid = session.get('user_id')
        if uid:
            chat_service.set_online(uid, request.sid)
            join_room(f'user_{uid}')
            sio.emit('user_online', {'user_id': uid}, skip_sid=request.sid)

    @sio.on('disconnect')
    def on_disconnect(reason=None):
        uid = session.get('user_id')
        if uid:
            chat_service.set_offline(uid)
            try:
                sio.emit('user_offline', {'user_id': uid})
            except Exception:
                pass

    @sio.on('join_conv')
    def on_join_conv(data):
        uid = session.get('user_id')
        if not uid:
            return
        cid = data.get('conv_id')
        if not cid:
            return
        member_ids = chat_service.get_members_cached(cid)
        if uid in member_ids:
            join_room(f'conv_{cid}')

    @sio.on('leave_conv')
    def on_leave_conv(data):
        cid = data.get('conv_id')
        if cid:
            leave_room(f'conv_{cid}')

    @sio.on('typing')
    def on_typing(data):
        cid = data.get('conv_id')
        uid = session.get('user_id')
        name = session.get('full_name') or session.get('username', '')
        if cid and uid:
            sio.emit('user_typing', {'user_id': uid, 'name': name, 'conv_id': cid}, room=f'conv_{cid}', skip_sid=request.sid)

    @sio.on('heartbeat')
    def on_heartbeat():
        uid = session.get('user_id')
        if uid:
            chat_service.set_online(uid, request.sid)
