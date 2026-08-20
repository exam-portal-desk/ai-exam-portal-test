"""
app/services/chat_service.py
Business logic for the chat feature — extracted from app/routes/chat.py
so the web (page shell) and API layers share one implementation instead
of the route file reaching into SQL directly. Behaviour matches the
original route file's helpers, with two fixes noted inline where the
architecture audit flagged real N+1/duplicate-query patterns (see
app/db/chat.py's docstring for the query-level detail):

  - get_conversations_for_user() now fetches every conversation's last
    message in one query instead of looping per-conversation.
  - remove_friend's DM lookup (see find_dm_conversation_id_with) fetches
    all candidate conversations' members in one query instead of looping.

In-process caches (_chat_rate, _online_users, _unread_buffer,
_member_cache) are unchanged from the original — still per-process, not
shared across multiple worker processes, same as before this refactor.
"""

import threading
import time
import re

from app.utils.datetime_service import now_utc_naive
import app.db.chat as chat_db
from app.services.image_storage_service import profile_photo_url_from_key

CHAT_RATE_LIMIT = 2
MAX_CHAT_MSG_LEN = 1000

_chat_rate: dict = {}
_chat_rate_lock = threading.Lock()

_online_users: dict = {}
_online_lock = threading.Lock()

_unread_buffer: dict = {}
_unread_lock = threading.Lock()

_member_cache: dict = {}
_member_cache_lock = threading.Lock()
MEMBER_CACHE_TTL = 60

# ── Deleted account placeholder ───────────────────────────────────────────────
# Used in get_messages() to show a placeholder for messages whose sender
# deleted their account. sender_id is NOT NULL in chat_messages, so instead
# of deleting these rows in user_deletion_service.py (which breaks group chat
# history), we keep the rows and identify them by sender_name sentinel.
DELETED_SENDER_NAME     = "__deleted_account__"   # internal sentinel stored in DB
DELETED_DISPLAY_NAME    = "Deleted Account"        # shown to users
DELETED_MESSAGE_CONTENT = "This user has deleted their account."


def sanitize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'[<>]', '', text)
    return re.sub(r'\s+', ' ', text)


def rate_ok(uid) -> bool:
    now = time.time()
    with _chat_rate_lock:
        if now - _chat_rate.get(uid, 0) < CHAT_RATE_LIMIT:
            return False
        _chat_rate[uid] = now
        return True


# ─────────────────────────────────────────────
# Member cache
# ─────────────────────────────────────────────

def get_members_cached(conv_id: int) -> list:
    now = time.time()
    with _member_cache_lock:
        entry = _member_cache.get(conv_id)
        if entry and now - entry['ts'] < MEMBER_CACHE_TTL:
            return entry['ids']
    ids = chat_db.get_member_ids(conv_id)
    with _member_cache_lock:
        _member_cache[conv_id] = {'ids': ids, 'ts': now}
    return ids


def invalidate_member_cache(conv_id: int) -> None:
    with _member_cache_lock:
        _member_cache.pop(conv_id, None)


# ─────────────────────────────────────────────
# Online presence
# ─────────────────────────────────────────────

def set_online(uid, sid) -> None:
    with _online_lock:
        _online_users[uid] = {'sid': sid, 'ts': time.time()}


def set_offline(uid) -> None:
    with _online_lock:
        _online_users.pop(uid, None)


def is_online(uid) -> bool:
    with _online_lock:
        entry = _online_users.get(uid)
        if not entry:
            return False
        return (time.time() - entry['ts']) < 120


def get_sid_for(uid):
    with _online_lock:
        entry = _online_users.get(uid)
        return entry['sid'] if entry else None


# ─────────────────────────────────────────────
# Unread buffering (flushed to DB every 5s by a background thread)
# ─────────────────────────────────────────────

def buffer_unread(conv_id: int, exclude_uid) -> None:
    member_ids = get_members_cached(conv_id)
    with _unread_lock:
        for uid in member_ids:
            if uid != exclude_uid:
                key = (uid, conv_id)
                _unread_buffer[key] = _unread_buffer.get(key, 0) + 1


def _flush_unread_loop():
    while True:
        time.sleep(5)
        with _unread_lock:
            if not _unread_buffer:
                continue
            snapshot = dict(_unread_buffer)
            _unread_buffer.clear()
        for (uid, conv_id), delta in snapshot.items():
            try:
                existing = chat_db.get_unread_row(uid, conv_id)
                if existing:
                    chat_db.set_unread_count_by_id(existing['id'], existing['count'] + delta)
                else:
                    chat_db.create_unread_row(uid, conv_id, delta)
            except Exception:
                pass


threading.Thread(target=_flush_unread_loop, daemon=True).start()


# ─────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────

def get_or_create_dm_conv(uid1: int, uid2: int) -> int:
    uid1_convs = chat_db.get_user_conversation_ids(uid1)
    if uid1_convs:
        uid2_match = chat_db.get_user_conversation_ids_in(uid2, uid1_convs)
        if uid2_match:
            dm_ids = chat_db.get_dm_conversation_ids_in(uid2_match)
            if dm_ids:
                return dm_ids[0]
    conv = chat_db.create_conversation(is_group=False, created_by=uid1)
    cid = conv['id']
    chat_db.add_members_bulk(cid, [uid1, uid2])
    return cid


def purge_conversation_fully(conv_id: int) -> None:
    """Hard-delete a conversation and all its child rows.
    Used by both remove_friend and user deletion cleanup (DM only).
    Group chats are NOT purged on user deletion — messages get placeholder instead."""
    invalidate_member_cache(conv_id)
    chat_db.purge_conversation_fully(conv_id)


def find_dm_conversation_id_with(my_uid: int, other_uid: int):
    """Locate the DM conversation between my_uid and other_uid, if any —
    including a self-only "ghost" DM left over after the other side removed
    the connection first. Fetches all candidate conversations' members in
    one query instead of looping and querying per-conversation."""
    my_conv_ids = chat_db.get_user_conversation_ids(my_uid)
    if not my_conv_ids:
        return None
    dm_conv_ids = chat_db.get_dm_conversation_ids_in(my_conv_ids)
    if not dm_conv_ids:
        return None

    all_members = chat_db.get_all_members_for_conversations(dm_conv_ids)
    members_by_conv: dict = {}
    for m in all_members:
        members_by_conv.setdefault(m['conversation_id'], []).append(m['user_id'])

    for cid in dm_conv_ids:
        member_ids = members_by_conv.get(cid, [])
        if other_uid in member_ids:
            return cid

    for cid in dm_conv_ids:
        if members_by_conv.get(cid, []) == [my_uid]:
            return cid

    return None


def get_conversations_for_user(uid: int) -> list:
    conv_ids = chat_db.get_user_conversation_ids(uid)
    if not conv_ids:
        return []

    convs = chat_db.get_conversations_by_ids(conv_ids)
    unread_map = chat_db.get_unread_map(uid)
    last_msgs = chat_db.get_last_messages_bulk(conv_ids)
    for row in last_msgs.values():
        if row.get('sender_name') == DELETED_SENDER_NAME:
            row['sender_name'] = DELETED_DISPLAY_NAME
            row['message'] = DELETED_MESSAGE_CONTENT

    all_members_res = chat_db.get_all_members_for_conversations(conv_ids)
    conv_member_map: dict = {}
    all_other_ids = []
    for m in all_members_res:
        conv_member_map.setdefault(m['conversation_id'], []).append(m['user_id'])
        if m['user_id'] != uid:
            all_other_ids.append(m['user_id'])

    from app.db.users import get_users_by_ids
    user_cache = {}
    if all_other_ids:
        # get_users_by_ids() returns a dict keyed by str(id), e.g. {"5": {...}} —
        # convert to int keys since every other lookup in this function (member_ids,
        # other_id) uses the int user_id from conv_member_map.
        ur = get_users_by_ids(list(set(all_other_ids)))
        user_cache = {int(uid_str): u for uid_str, u in ur.items()}

    result = []
    ghost_conv_ids = []

    for c in convs:
        cid = c['id']
        member_ids = conv_member_map.get(cid, [])
        other_id = None
        members_list = []

        photo_url = None
        if c['is_group']:
            name = c['group_name'] or 'Group'
            members_list = [
                {'id': m, 'name': 'You' if m == uid else (user_cache.get(m, {}).get('full_name') or user_cache.get(m, {}).get('username') or '?'),
                 'photo_url': None if m == uid else profile_photo_url_from_key(user_cache.get(m, {}).get('profile_photo_key'))}
                for m in member_ids
            ]
        else:
            other_ids = [m for m in member_ids if m != uid]
            other_id = other_ids[0] if other_ids else None

            if other_id is None or other_id not in user_cache:
                ghost_conv_ids.append(cid)
                continue

            u = user_cache.get(other_id, {})
            name = u.get('full_name') or u.get('username') or 'Chat'
            photo_url = profile_photo_url_from_key(u.get('profile_photo_key'))

        result.append({
            'id': cid, 'name': name, 'is_group': c['is_group'], 'created_by': c.get('created_by'),
            'unread': unread_map.get(cid, 0), 'last_message': last_msgs.get(cid),
            'online': is_online(other_id) if not c['is_group'] else False,
            'other_id': other_id, 'members': members_list, 'photo_url': photo_url,
        })

    if ghost_conv_ids:
        def _cleanup_ghosts(ids):
            for cid in ids:
                try:
                    purge_conversation_fully(cid)
                    print(f'[Chat] Purged ghost conversation {cid}')
                except Exception as e:
                    print(f'[Chat] Failed to purge ghost conv {cid}: {e}')
        threading.Thread(target=_cleanup_ghosts, args=(ghost_conv_ids,), daemon=True).start()

    result.sort(key=lambda x: x['last_message']['created_at'] if x['last_message'] else '0', reverse=True)
    return result


# ─────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────

def normalize_message(m: dict, current_uid) -> dict:
    """Normalize a raw chat_messages row for API response.
    Handles the deleted-account placeholder sentinel gracefully."""
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
