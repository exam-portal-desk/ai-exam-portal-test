from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for
from flask_socketio import join_room, leave_room
from app.db import fetch_one, fetch_all, execute, insert_returning, insert_many
import threading, time, re
from app.utils.datetime_service import now_utc_naive

chat_bp = Blueprint('chat', __name__)
socketio = None

CHAT_RATE_LIMIT = 2
MAX_CHAT_MSG_LEN = 1000
_chat_rate = {}
_chat_rate_lock = threading.Lock()
_online_users = {}
_online_lock = threading.Lock()

_unread_buffer = {}
_unread_lock = threading.Lock()

_member_cache = {}
_member_cache_lock = threading.Lock()
MEMBER_CACHE_TTL = 60

# ── Deleted account placeholder ───────────────────────────────────────────────
# Used in get_messages() to show a placeholder for messages whose sender
# deleted their account.  sender_id is NOT NULL in chat_messages, so instead
# of deleting these rows in user_deletion_service.py (which breaks group chat
# history), we keep the rows and identify them by sender_name sentinel.
DELETED_SENDER_NAME     = "__deleted_account__"   # internal sentinel stored in DB
DELETED_DISPLAY_NAME    = "Deleted Account"        # shown to users
DELETED_MESSAGE_CONTENT = "This user has deleted their account."


def init_chat_socketio(sio):
    global socketio
    socketio = sio

def _sanitize(text):
    text = text.strip()
    text = re.sub(r'[<>]', '', text)
    return re.sub(r'\s+', ' ', text)

def _rate_ok(uid):
    now = time.time()
    with _chat_rate_lock:
        if now - _chat_rate.get(uid, 0) < CHAT_RATE_LIMIT:
            return False
        _chat_rate[uid] = now
        return True

def _uid():
    return session.get('user_id')

def _uname():
    return session.get('full_name') or session.get('username', 'User')

def _get_members_cached(conv_id):
    now = time.time()
    with _member_cache_lock:
        entry = _member_cache.get(conv_id)
        if entry and now - entry['ts'] < MEMBER_CACHE_TTL:
            return entry['ids']
    rows = fetch_all('SELECT user_id FROM chat_members WHERE conversation_id=%s', (conv_id,))
    ids = [m['user_id'] for m in rows]
    with _member_cache_lock:
        _member_cache[conv_id] = {'ids': ids, 'ts': now}
    return ids

def _invalidate_member_cache(conv_id):
    with _member_cache_lock:
        _member_cache.pop(conv_id, None)

def _get_or_create_dm_conv(uid1, uid2):
    uid1_convs = fetch_all('SELECT conversation_id FROM chat_members WHERE user_id=%s', (uid1,))
    if uid1_convs:
        cids = [r['conversation_id'] for r in uid1_convs]
        uid2_match = fetch_all(
            'SELECT conversation_id FROM chat_members WHERE user_id=%s AND conversation_id = ANY(%s)', (uid2, cids)
        )
        if uid2_match:
            match_cids = [r['conversation_id'] for r in uid2_match]
            dm = fetch_all(
                'SELECT id FROM chat_conversations WHERE is_group=%s AND id = ANY(%s)', (False, match_cids)
            )
            if dm:
                return dm[0]['id']
    conv = insert_returning('chat_conversations', {'is_group': False, 'created_by': uid1})
    cid = conv['id']
    insert_many('chat_members', [
        {'conversation_id': cid, 'user_id': uid1},
        {'conversation_id': cid, 'user_id': uid2},
    ])
    return cid

def _buffer_unread(conv_id, exclude_uid):
    member_ids = _get_members_cached(conv_id)
    with _unread_lock:
        for uid in member_ids:
            if uid != exclude_uid:
                key = (uid, conv_id)
                _unread_buffer[key] = _unread_buffer.get(key, 0) + 1

def _flush_unread():
    while True:
        time.sleep(5)
        with _unread_lock:
            if not _unread_buffer:
                continue
            snapshot = dict(_unread_buffer)
            _unread_buffer.clear()
        for (uid, conv_id), delta in snapshot.items():
            try:
                existing = fetch_one(
                    'SELECT id,count FROM chat_unread WHERE user_id=%s AND conversation_id=%s', (uid, conv_id)
                )
                if existing:
                    execute('UPDATE chat_unread SET count=%s WHERE id=%s', (existing['count'] + delta, existing['id']))
                else:
                    insert_returning('chat_unread', {'user_id': uid, 'conversation_id': conv_id, 'count': delta})
            except:
                pass

threading.Thread(target=_flush_unread, daemon=True).start()

def _set_online(uid, sid):
    with _online_lock:
        _online_users[uid] = {'sid': sid, 'ts': time.time()}

def _set_offline(uid):
    with _online_lock:
        _online_users.pop(uid, None)

def _is_online(uid):
    with _online_lock:
        entry = _online_users.get(uid)
        if not entry:
            return False
        return (time.time() - entry['ts']) < 120

def _emit(event, data, room, skip_uid=None):
    if not socketio:
        return
    skip_sid = None
    if skip_uid:
        with _online_lock:
            entry = _online_users.get(skip_uid)
            skip_sid = entry['sid'] if entry else None
    try:
        socketio.emit(event, data, room=room, skip_sid=skip_sid)
    except:
        socketio.emit(event, data, room=room)


def _purge_conversation_fully(conv_id):
    """
    Hard-delete a conversation and all its child rows.
    Used by both remove_friend and user deletion cleanup (DM only).
    Group chats are NOT purged on user deletion — messages get placeholder instead.
    """
    _invalidate_member_cache(conv_id)
    execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_members WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_unread WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_visibility WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))


def _normalize_message(m, current_uid):
    """
    Normalize a raw chat_messages row for API response.
    Handles the deleted-account placeholder sentinel gracefully.
    """
    is_deleted_account = (m.get('sender_name') == DELETED_SENDER_NAME)

    if is_deleted_account:
        m['sender_name']        = DELETED_DISPLAY_NAME
        m['message']            = DELETED_MESSAGE_CONTENT
        m['is_deleted_account'] = True
        m['is_own']             = False
        # Clear reply preview if it pointed to a deleted-account message
        # so the UI doesn't show "[deleted account]: This user deleted..." as quote
        if m.get('reply_to_name') == DELETED_SENDER_NAME:
            m['reply_to_name'] = DELETED_DISPLAY_NAME
            m['reply_to_text'] = DELETED_MESSAGE_CONTENT
    else:
        m['is_deleted_account'] = False
        m['is_own'] = (m.get('sender_id') == current_uid)

    m.pop('sender_id', None)
    return m


@chat_bp.route('/chat')
def chat_page():
    if not _uid():
        return redirect(url_for('login'))
    return render_template('chat.html')


@chat_bp.route('/api/chat/search')
def search_users():
    if not _uid():
        return jsonify({'success': False}), 401
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'success': True, 'users': []})
    try:
        res = fetch_all(
            'SELECT id,username,full_name FROM users WHERE username ILIKE %s AND id != %s LIMIT 10',
            (f'%{q}%', _uid()),
        )
        if not res:
            return jsonify({'success': True, 'users': []})
        user_ids = [u['id'] for u in res]
        conns = fetch_all(
            'SELECT requester_id,recipient_id,status FROM chat_connections '
            'WHERE (requester_id=%s AND recipient_id = ANY(%s)) OR (requester_id = ANY(%s) AND recipient_id=%s)',
            (_uid(), user_ids, user_ids, _uid()),
        )
        conn_map = {}
        for c in conns:
            other = c['recipient_id'] if c['requester_id'] == _uid() else c['requester_id']
            conn_map[other] = c['status']
        users = [{'id': u['id'], 'username': u['username'], 'full_name': u['full_name'],
                  'online': _is_online(u['id']), 'connection_status': conn_map.get(u['id'])} for u in res]
        return jsonify({'success': True, 'users': users})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/request', methods=['POST'])
def send_request():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    rid = data.get('recipient_id')
    if not rid or rid == _uid():
        return jsonify({'success': False}), 400
    try:
        existing = fetch_all(
            'SELECT id,status FROM chat_connections '
            'WHERE (requester_id=%s AND recipient_id=%s) OR (requester_id=%s AND recipient_id=%s)',
            (_uid(), rid, rid, _uid()),
        )
        if existing:
            row = existing[0]
            if row['status'] == 'pending':
                return jsonify({'success': False, 'message': 'Request already sent'}), 409
            elif row['status'] == 'accepted':
                return jsonify({'success': False, 'message': 'Already connected'}), 409
            elif row['status'] == 'rejected':
                execute(
                    'UPDATE chat_connections SET status=%s, requester_id=%s, recipient_id=%s, updated_at=%s WHERE id=%s',
                    ('pending', _uid(), rid, now_utc_naive().isoformat(), row['id']),
                )
                if socketio:
                    socketio.emit('chat_request', {'from_id': _uid(), 'from_name': _uname()}, room=f'user_{rid}')
                return jsonify({'success': True})
        insert_returning('chat_connections', {'requester_id': _uid(), 'recipient_id': rid, 'status': 'pending'})
        if socketio:
            socketio.emit('chat_request', {'from_id': _uid(), 'from_name': _uname()}, room=f'user_{rid}')
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/request/<int:conn_id>', methods=['PUT'])
def respond_request(conn_id):
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    action = data.get('action')
    if action not in ('accept', 'reject'):
        return jsonify({'success': False}), 400
    try:
        row = fetch_one('SELECT * FROM chat_connections WHERE id=%s', (conn_id,))
        if not row or row['recipient_id'] != _uid():
            return jsonify({'success': False}), 403
        status = 'accepted' if action == 'accept' else 'rejected'
        execute('UPDATE chat_connections SET status=%s, updated_at=%s WHERE id=%s',
                (status, now_utc_naive().isoformat(), conn_id))
        req_id = row['requester_id']
        if action == 'accept':
            conv_id = _get_or_create_dm_conv(_uid(), req_id)
            if socketio:
                socketio.emit('request_accepted', {'by_name': _uname(), 'conv_id': conv_id}, room=f'user_{req_id}')
            return jsonify({'success': True, 'conv_id': conv_id})
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/requests/inbox')
def inbox():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        res = fetch_all(
            'SELECT id,requester_id,status,created_at FROM chat_connections WHERE recipient_id=%s AND status=%s',
            (_uid(), 'pending'),
        )
        if not res:
            return jsonify({'success': True, 'requests': []})
        req_ids = [r['requester_id'] for r in res]
        users_res = fetch_all('SELECT id,username,full_name FROM users WHERE id = ANY(%s)', (req_ids,))
        user_map = {u['id']: u for u in users_res}
        requests = [{'conn_id': r['id'], 'from_id': r['requester_id'],
                     'from_name': user_map.get(r['requester_id'], {}).get('full_name') or user_map.get(r['requester_id'], {}).get('username') or 'User',
                     'created_at': r['created_at']} for r in res]
        return jsonify({'success': True, 'requests': requests})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/conversations')
def get_conversations():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        members = fetch_all('SELECT conversation_id FROM chat_members WHERE user_id=%s', (_uid(),))
        conv_ids = [m['conversation_id'] for m in members]
        if not conv_ids:
            return jsonify({'success': True, 'conversations': []})

        convs = fetch_all('SELECT * FROM chat_conversations WHERE id = ANY(%s)', (conv_ids,))
        unread_res = fetch_all('SELECT conversation_id,count FROM chat_unread WHERE user_id=%s', (_uid(),))
        unread_map = {u['conversation_id']: u['count'] for u in unread_res}

        last_msgs = {}
        for cid in conv_ids:
            lm = fetch_all(
                'SELECT message,sender_name,created_at FROM chat_messages '
                'WHERE conversation_id=%s AND is_deleted=%s ORDER BY created_at DESC LIMIT 1',
                (cid, False),
            )
            if lm:
                row = lm[0]
                # Show placeholder in last-message preview for deleted accounts
                if row.get('sender_name') == DELETED_SENDER_NAME:
                    row['sender_name'] = DELETED_DISPLAY_NAME
                    row['message']     = DELETED_MESSAGE_CONTENT
                last_msgs[cid] = row

        all_members_res = fetch_all('SELECT conversation_id,user_id FROM chat_members WHERE conversation_id = ANY(%s)', (conv_ids,))
        conv_member_map = {}
        all_other_ids = []
        for m in all_members_res:
            conv_member_map.setdefault(m['conversation_id'], []).append(m['user_id'])
            if m['user_id'] != _uid():
                all_other_ids.append(m['user_id'])

        user_cache = {}
        if all_other_ids:
            ur = fetch_all('SELECT id,full_name,username FROM users WHERE id = ANY(%s)', (list(set(all_other_ids)),))
            user_cache = {u['id']: u for u in ur}

        result = []
        ghost_conv_ids = []

        for c in convs:
            cid = c['id']
            member_ids = conv_member_map.get(cid, [])
            other_id = None
            members_list = []

            if c['is_group']:
                name = c['group_name'] or 'Group'
                members_list = [
                    {'id': uid, 'name': 'You' if uid == _uid() else (user_cache.get(uid, {}).get('full_name') or user_cache.get(uid, {}).get('username') or '?')}
                    for uid in member_ids
                ]
            else:
                other_ids = [uid for uid in member_ids if uid != _uid()]
                other_id = other_ids[0] if other_ids else None

                if other_id is None or other_id not in user_cache:
                    ghost_conv_ids.append(cid)
                    continue

                u = user_cache.get(other_id, {})
                name = u.get('full_name') or u.get('username') or 'Chat'

            result.append({
                'id': cid, 'name': name, 'is_group': c['is_group'], 'created_by': c.get('created_by'),
                'unread': unread_map.get(cid, 0), 'last_message': last_msgs.get(cid),
                'online': _is_online(other_id) if not c['is_group'] else False,
                'other_id': other_id, 'members': members_list
            })

        if ghost_conv_ids:
            def _cleanup_ghosts(ids):
                for cid in ids:
                    try:
                        _purge_conversation_fully(cid)
                        print(f'[Chat] Purged ghost conversation {cid}')
                    except Exception as e:
                        print(f'[Chat] Failed to purge ghost conv {cid}: {e}')
            import threading as _t
            _t.Thread(target=_cleanup_ghosts, args=(ghost_conv_ids,), daemon=True).start()

        result.sort(key=lambda x: x['last_message']['created_at'] if x['last_message'] else '0', reverse=True)
        return jsonify({'success': True, 'conversations': result})
    except Exception as e:
        print(f'[Chat] convs error: {e}')
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/messages/<int:conv_id>')
def get_messages(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    member = fetch_all('SELECT id FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
    if not member:
        return jsonify({'success': False}), 403
    try:
        member_row = fetch_all('SELECT joined_at FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
        cleared_at = member_row[0]['joined_at'] if member_row else None
        before = request.args.get('before')

        # Fetch sender_id too so we can detect is_own and deleted accounts
        query = (
            'SELECT id,sender_id,sender_name,message,created_at,is_edited,reply_to_id,reply_to_text,reply_to_name '
            'FROM chat_messages WHERE conversation_id=%s AND is_deleted=%s'
        )
        params = [conv_id, False]
        if before:
            query += ' AND created_at < %s'
            params.append(before)
        if cleared_at:
            query += ' AND created_at > %s'
            params.append(cleared_at)
        query += ' ORDER BY created_at DESC LIMIT 40'

        res = fetch_all(query, params)
        msgs = list(reversed(res))
        uid = _uid()

        # Normalize each message — handles deleted account sentinel
        msgs = [_normalize_message(m, uid) for m in msgs]

        execute('UPDATE chat_unread SET count=%s WHERE user_id=%s AND conversation_id=%s', (0, uid, conv_id))
        return jsonify({'success': True, 'messages': msgs})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/messages/<int:conv_id>', methods=['POST'])
def send_message(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    uid = _uid()
    if not _rate_ok(uid):
        return jsonify({'success': False, 'message': 'Slow down'}), 429
    member = fetch_all('SELECT id FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, uid))
    if not member:
        return jsonify({'success': False}), 403
    data = request.get_json() or {}
    msg = data.get('message', '').strip()
    if not msg or len(msg) > MAX_CHAT_MSG_LEN:
        return jsonify({'success': False}), 400
    msg = _sanitize(msg)
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
        res = insert_returning('chat_messages', record)
        msg_id = res['id']
        _buffer_unread(conv_id, uid)
        broadcast = {
            'id': msg_id, 'sender_name': name, 'message': msg, 'created_at': now,
            'is_own': False, 'conv_id': conv_id,
            'is_deleted_account': False,
            'reply_to_id': reply_to_id, 'reply_to_text': reply_to_text, 'reply_to_name': reply_to_name
        }
        _emit('chat_message', broadcast, room=f'conv_{conv_id}', skip_uid=uid)
        return jsonify({'success': True, 'message': {**broadcast, 'is_own': True}})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/messages/<int:msg_id>/edit', methods=['PUT'])
def edit_message(msg_id):
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    new_text = data.get('message', '').strip()
    if not new_text or len(new_text) > MAX_CHAT_MSG_LEN:
        return jsonify({'success': False, 'message': 'Invalid message'}), 400
    new_text = _sanitize(new_text)
    try:
        row = fetch_one('SELECT sender_id,conversation_id,sender_name FROM chat_messages WHERE id=%s', (msg_id,))
        if not row or row['sender_id'] != _uid():
            return jsonify({'success': False}), 403
        # Prevent editing a deleted-account placeholder
        if row.get('sender_name') == DELETED_SENDER_NAME:
            return jsonify({'success': False, 'message': 'Cannot edit this message'}), 403
        conv_id = row['conversation_id']
        execute('UPDATE chat_messages SET message=%s, is_edited=%s WHERE id=%s', (new_text, True, msg_id))
        _emit('msg_edited', {'msg_id': msg_id, 'message': new_text, 'conv_id': conv_id}, room=f'conv_{conv_id}')
        return jsonify({'success': True, 'message': new_text})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/messages/<int:msg_id>', methods=['DELETE'])
def delete_message(msg_id):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        row = fetch_one('SELECT sender_id,conversation_id,sender_name FROM chat_messages WHERE id=%s', (msg_id,))
        if not row or row['sender_id'] != _uid():
            return jsonify({'success': False}), 403
        # Prevent deleting a deleted-account placeholder
        if row.get('sender_name') == DELETED_SENDER_NAME:
            return jsonify({'success': False, 'message': 'Cannot delete this message'}), 403
        conv_id = row['conversation_id']
        execute('UPDATE chat_messages SET is_deleted=%s WHERE id=%s', (True, msg_id))
        _emit('msg_deleted', {'msg_id': msg_id, 'conv_id': conv_id}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/conversations/<int:conv_id>/clear', methods=['DELETE'])
def clear_chat(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    member = fetch_all('SELECT id FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
    if not member:
        return jsonify({'success': False}), 403
    try:
        now = now_utc_naive().isoformat()
        execute('UPDATE chat_members SET joined_at=%s WHERE conversation_id=%s AND user_id=%s', (now, conv_id, _uid()))
        execute('UPDATE chat_unread SET count=%s WHERE user_id=%s AND conversation_id=%s', (0, _uid(), conv_id))
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/conversations/<int:conv_id>/read', methods=['POST'])
def mark_read(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    member = fetch_all('SELECT id FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
    if not member:
        return jsonify({'success': False}), 403
    try:
        execute('UPDATE chat_unread SET count=%s WHERE user_id=%s AND conversation_id=%s', (0, _uid(), conv_id))
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/friend/<int:other_id>', methods=['DELETE'])
def remove_friend(other_id):
    """
    Remove a friend connection and purge the shared DM conversation.
    Group chats are unaffected — only the DM between these two users is removed.
    """
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conn = fetch_all(
            'SELECT id FROM chat_connections WHERE (requester_id=%s AND recipient_id=%s) OR (requester_id=%s AND recipient_id=%s)',
            (_uid(), other_id, other_id, _uid()),
        )
        if conn:
            execute('DELETE FROM chat_connections WHERE id=%s', (conn[0]['id'],))

        conv_id = None
        my_dms_res = fetch_all('SELECT conversation_id FROM chat_members WHERE user_id=%s', (_uid(),))
        if my_dms_res:
            my_conv_ids = [r['conversation_id'] for r in my_dms_res]
            dm_convs_res = fetch_all('SELECT id FROM chat_conversations WHERE is_group=%s AND id = ANY(%s)', (False, my_conv_ids))
            dm_conv_ids = [r['id'] for r in dm_convs_res]
            if dm_conv_ids:
                other_member_res = fetch_all(
                    'SELECT conversation_id FROM chat_members WHERE user_id=%s AND conversation_id = ANY(%s)', (other_id, dm_conv_ids)
                )
                if other_member_res:
                    conv_id = other_member_res[0]['conversation_id']
                else:
                    for cid in dm_conv_ids:
                        all_members_res = fetch_all('SELECT user_id FROM chat_members WHERE conversation_id=%s', (cid,))
                        member_ids = [m['user_id'] for m in all_members_res]
                        if member_ids == [_uid()]:
                            conv_id = cid
                            break

        if conv_id:
            _purge_conversation_fully(conv_id)
            if socketio:
                socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{other_id}')

        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] remove friend error: {e}')
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/group/<int:conv_id>/exit', methods=['DELETE'])
def exit_group(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = fetch_all('SELECT created_by,group_name FROM chat_conversations WHERE id=%s', (conv_id,))
        if not conv:
            return jsonify({'success': False}), 404
        is_creator = conv[0]['created_by'] == _uid()
        remaining = fetch_all('SELECT user_id FROM chat_members WHERE conversation_id=%s AND user_id != %s', (conv_id, _uid()))
        execute('DELETE FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
        execute('DELETE FROM chat_unread WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
        _invalidate_member_cache(conv_id)
        if is_creator and remaining:
            execute('UPDATE chat_conversations SET created_by=%s WHERE id=%s', (remaining[0]['user_id'], conv_id))
        if socketio:
            socketio.emit('member_left', {'conv_id': conv_id, 'user_name': _uname()}, room=f'conv_{conv_id}')
        if not remaining:
            execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
            execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))
        if socketio:
            socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{_uid()}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] exit group error: {e}')
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/group/<int:conv_id>', methods=['DELETE'])
def delete_group(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = fetch_all('SELECT created_by FROM chat_conversations WHERE id=%s', (conv_id,))
        if not conv or conv[0]['created_by'] != _uid():
            return jsonify({'success': False, 'message': 'Only group creator can delete the group'}), 403
        members = fetch_all('SELECT user_id FROM chat_members WHERE conversation_id=%s', (conv_id,))
        _invalidate_member_cache(conv_id)
        execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
        execute('DELETE FROM chat_members WHERE conversation_id=%s', (conv_id,))
        execute('DELETE FROM chat_unread WHERE conversation_id=%s', (conv_id,))
        execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))
        if socketio:
            for m in members:
                socketio.emit('conversation_removed', {'conv_id': conv_id}, room=f'user_{m["user_id"]}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] delete group error: {e}')
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/group', methods=['POST'])
def create_group():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    member_ids = data.get('member_ids', [])
    if not name or not member_ids:
        return jsonify({'success': False}), 400
    name = _sanitize(name)[:50]
    try:
        conv = insert_returning('chat_conversations', {'is_group': True, 'group_name': name, 'created_by': _uid()})
        cid = conv['id']
        all_members = list(set([_uid()] + member_ids))
        insert_many('chat_members', [{'conversation_id': cid, 'user_id': m} for m in all_members])
        for m in all_members:
            if m != _uid() and socketio:
                socketio.emit('added_to_group', {'conv_id': cid, 'group_name': name}, room=f'user_{m}')
        return jsonify({'success': True, 'conv_id': cid})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/group/<int:conv_id>/members', methods=['POST'])
def add_group_member(conv_id):
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.get_json() or {}
    new_uid = data.get('user_id')
    if not new_uid:
        return jsonify({'success': False}), 400
    try:
        conv = fetch_all('SELECT created_by,group_name FROM chat_conversations WHERE id=%s AND is_group=%s', (conv_id, True))
        if not conv:
            return jsonify({'success': False, 'message': 'Group not found'}), 404
        if conv[0]['created_by'] != _uid():
            admin_check = fetch_all('SELECT role FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
            if not admin_check or admin_check[0].get('role') != 'admin':
                return jsonify({'success': False, 'message': 'Only admins can add members'}), 403
        already = fetch_all('SELECT id FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, new_uid))
        if already:
            return jsonify({'success': False, 'message': 'User already in group'}), 409
        insert_returning('chat_members', {'conversation_id': conv_id, 'user_id': new_uid})
        _invalidate_member_cache(conv_id)
        group_name = conv[0]['group_name'] or 'Group'
        if socketio:
            socketio.emit('added_to_group', {'conv_id': conv_id, 'group_name': group_name}, room=f'user_{new_uid}')
            socketio.emit('member_joined', {'conv_id': conv_id, 'user_id': new_uid}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except Exception as e:
        print(f'[Chat] add member error: {e}')
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/group/<int:conv_id>/members/<int:target_uid>', methods=['DELETE'])
def remove_group_member(conv_id, target_uid):
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        conv = fetch_all('SELECT created_by FROM chat_conversations WHERE id=%s', (conv_id,))
        if not conv:
            return jsonify({'success': False}), 404
        is_admin = conv[0]['created_by'] == _uid()
        if not is_admin:
            admin_check = fetch_all('SELECT role FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, _uid()))
            is_admin = bool(admin_check and admin_check[0].get('role') == 'admin')
        if not is_admin and target_uid != _uid():
            return jsonify({'success': False}), 403
        execute('DELETE FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, target_uid))
        execute('DELETE FROM chat_unread WHERE conversation_id=%s AND user_id=%s', (conv_id, target_uid))
        _invalidate_member_cache(conv_id)
        if socketio:
            socketio.emit('removed_from_group', {'conv_id': conv_id}, room=f'user_{target_uid}')
            socketio.emit('member_left', {'conv_id': conv_id, 'user_id': target_uid}, room=f'conv_{conv_id}')
        return jsonify({'success': True})
    except:
        return jsonify({'success': False}), 500


@chat_bp.route('/api/chat/unread_count')
def unread_count():
    if not _uid():
        return jsonify({'success': False}), 401
    try:
        unread_res = fetch_all('SELECT count FROM chat_unread WHERE user_id=%s', (_uid(),))
        total = sum(r['count'] for r in unread_res)
        pending_res = fetch_one('SELECT COUNT(*) AS count FROM chat_connections WHERE recipient_id=%s AND status=%s', (_uid(), 'pending'))
        pending_count = pending_res['count'] if pending_res else 0
        return jsonify({'success': True, 'unread': total, 'requests': pending_count})
    except:
        return jsonify({'success': True, 'unread': 0, 'requests': 0})


@chat_bp.route('/api/chat/online_status')
def online_status():
    if not _uid():
        return jsonify({'success': False}), 401
    data = request.args.get('ids', '')
    try:
        ids = [int(x) for x in data.split(',') if x.strip()]
        result = {uid: _is_online(uid) for uid in ids}
        return jsonify({'success': True, 'status': result})
    except:
        return jsonify({'success': False}), 400


def register_chat_socketio_events(sio):
    @sio.on('connect')
    def on_connect(auth=None):
        uid = session.get('user_id')
        if uid:
            _set_online(uid, request.sid)
            join_room(f'user_{uid}')
            sio.emit('user_online', {'user_id': uid}, skip_sid=request.sid)

    @sio.on('disconnect')
    def on_disconnect(reason=None):
        uid = session.get('user_id')
        if uid:
            _set_offline(uid)
            try:
                sio.emit('user_offline', {'user_id': uid})
            except:
                pass

    @sio.on('join_conv')
    def on_join_conv(data):
        uid = session.get('user_id')
        if not uid:
            return
        cid = data.get('conv_id')
        if not cid:
            return
        member_ids = _get_members_cached(cid)
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
            _set_online(uid, request.sid)
