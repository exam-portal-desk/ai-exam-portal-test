"""
app/db/chat.py
Data access for the peer-to-peer/group chat feature — extracted from
app/routes/chat.py, which previously called the low-level
fetch_one/fetch_all/execute/insert_returning/insert_many primitives
directly with inline SQL. Behaviour is unchanged except where noted:

  - get_last_messages_bulk() replaces a per-conversation query loop
    (one round trip per conversation) with a single DISTINCT ON query.
  - get_membership() replaces two near-identical membership queries
    (an existence check, then a separate joined_at fetch) with one query
    that returns both.
  - get_all_members_for_conversations() lets remove_friend's DM lookup
    fetch all candidate conversations' members in one query instead of
    looping and querying per-conversation.

Each is flagged in the architecture audit as an N+1/duplicate-query
pattern; fixing them here (once, in the data layer) benefits every
caller instead of requiring each route to remember to batch.
"""

from app.db import fetch_one, fetch_all, execute, insert_returning, insert_many


# ─────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────

def get_conversation(conv_id: int) -> dict | None:
    return fetch_one('SELECT * FROM chat_conversations WHERE id=%s', (conv_id,))


def get_conversations_by_ids(conv_ids: list) -> list:
    return fetch_all('SELECT * FROM chat_conversations WHERE id = ANY(%s)', (conv_ids,))


def create_conversation(is_group: bool, created_by: int, group_name: str | None = None) -> dict | None:
    data = {'is_group': is_group, 'created_by': created_by}
    if group_name is not None:
        data['group_name'] = group_name
    return insert_returning('chat_conversations', data)


def update_conversation_creator(conv_id: int, new_creator_id: int) -> None:
    execute('UPDATE chat_conversations SET created_by=%s WHERE id=%s', (new_creator_id, conv_id))


def get_dm_conversation_ids_in(conv_ids: list) -> list:
    """Of the given conversation ids, which are (non-group) DMs."""
    rows = fetch_all('SELECT id FROM chat_conversations WHERE is_group=%s AND id = ANY(%s)', (False, conv_ids))
    return [r['id'] for r in rows]


def delete_group_conversation(conv_id: int) -> None:
    """Hard-delete a group's messages/members/unread/conversation rows.
    Deliberately does NOT touch chat_visibility (matches the original
    delete_group() behaviour, distinct from _purge_conversation_fully)."""
    execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_members WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_unread WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))


def delete_messages_and_conversation(conv_id: int) -> None:
    """Used by exit_group() when the last member just left — members/unread
    for that user were already removed by the caller before this runs."""
    execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))


def purge_conversation_fully(conv_id: int) -> None:
    """Hard-delete a conversation and ALL its child rows, including
    chat_visibility. Used by remove_friend (DM) and ghost-conversation
    cleanup."""
    execute('DELETE FROM chat_messages WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_members WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_unread WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_visibility WHERE conversation_id=%s', (conv_id,))
    execute('DELETE FROM chat_conversations WHERE id=%s', (conv_id,))


# ─────────────────────────────────────────────
# Members
# ─────────────────────────────────────────────

def get_member_ids(conv_id: int) -> list:
    rows = fetch_all('SELECT user_id FROM chat_members WHERE conversation_id=%s', (conv_id,))
    return [r['user_id'] for r in rows]


def get_all_members_for_conversations(conv_ids: list) -> list:
    """[{'conversation_id', 'user_id'}, ...] for every member across all given conversations."""
    return fetch_all('SELECT conversation_id,user_id FROM chat_members WHERE conversation_id = ANY(%s)', (conv_ids,))


def get_user_conversation_ids(user_id: int) -> list:
    rows = fetch_all('SELECT conversation_id FROM chat_members WHERE user_id=%s', (user_id,))
    return [r['conversation_id'] for r in rows]


def get_user_conversation_ids_in(user_id: int, conv_ids: list) -> list:
    rows = fetch_all(
        'SELECT conversation_id FROM chat_members WHERE user_id=%s AND conversation_id = ANY(%s)',
        (user_id, conv_ids),
    )
    return [r['conversation_id'] for r in rows]


def get_membership(conv_id: int, user_id: int) -> dict | None:
    """{'id', 'joined_at'} if the user is a member of this conversation, else None.
    Single query — replaces a separate existence check + joined_at fetch."""
    return fetch_one('SELECT id,joined_at FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, user_id))


def get_member_role(conv_id: int, user_id: int) -> str | None:
    row = fetch_one('SELECT role FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, user_id))
    return row.get('role') if row else None


def add_member(conv_id: int, user_id: int) -> dict | None:
    return insert_returning('chat_members', {'conversation_id': conv_id, 'user_id': user_id})


def add_members_bulk(conv_id: int, user_ids: list) -> int:
    return insert_many('chat_members', [{'conversation_id': conv_id, 'user_id': uid} for uid in user_ids])


def remove_member(conv_id: int, user_id: int) -> None:
    execute('DELETE FROM chat_members WHERE conversation_id=%s AND user_id=%s', (conv_id, user_id))


def update_joined_at(conv_id: int, user_id: int, joined_at_iso: str) -> None:
    execute('UPDATE chat_members SET joined_at=%s WHERE conversation_id=%s AND user_id=%s', (joined_at_iso, conv_id, user_id))


# ─────────────────────────────────────────────
# Messages
# ─────────────────────────────────────────────

def get_last_messages_bulk(conv_ids: list) -> dict:
    """{conversation_id: {'message','sender_name','created_at'}} for the most
    recent non-deleted message in each conversation — one query for all
    conversations instead of one query per conversation."""
    rows = fetch_all(
        'SELECT DISTINCT ON (conversation_id) conversation_id, message, sender_name, created_at '
        'FROM chat_messages WHERE conversation_id = ANY(%s) AND is_deleted=%s '
        'ORDER BY conversation_id, created_at DESC',
        (conv_ids, False),
    )
    return {r['conversation_id']: r for r in rows}


def get_messages(conv_id: int, before: str | None, cleared_at: str | None, limit: int = 40) -> list:
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
    query += ' ORDER BY created_at DESC LIMIT %s'
    params.append(limit)
    return fetch_all(query, params)


def insert_message(record: dict) -> dict | None:
    return insert_returning('chat_messages', record)


def get_message_owner(msg_id: int) -> dict | None:
    return fetch_one('SELECT sender_id,conversation_id,sender_name FROM chat_messages WHERE id=%s', (msg_id,))


def update_message_text(msg_id: int, message: str) -> None:
    execute('UPDATE chat_messages SET message=%s, is_edited=%s WHERE id=%s', (message, True, msg_id))


def soft_delete_message(msg_id: int) -> None:
    execute('UPDATE chat_messages SET is_deleted=%s WHERE id=%s', (True, msg_id))


# ─────────────────────────────────────────────
# Unread counters
# ─────────────────────────────────────────────

def get_unread_map(user_id: int) -> dict:
    rows = fetch_all('SELECT conversation_id,count FROM chat_unread WHERE user_id=%s', (user_id,))
    return {r['conversation_id']: r['count'] for r in rows}


def get_unread_counts(user_id: int) -> list:
    return fetch_all('SELECT count FROM chat_unread WHERE user_id=%s', (user_id,))


def reset_unread(user_id: int, conv_id: int) -> None:
    execute('UPDATE chat_unread SET count=%s WHERE user_id=%s AND conversation_id=%s', (0, user_id, conv_id))


def delete_unread(conv_id: int, user_id: int) -> None:
    execute('DELETE FROM chat_unread WHERE conversation_id=%s AND user_id=%s', (conv_id, user_id))


def get_unread_row(user_id: int, conv_id: int) -> dict | None:
    return fetch_one('SELECT id,count FROM chat_unread WHERE user_id=%s AND conversation_id=%s', (user_id, conv_id))


def set_unread_count_by_id(row_id: int, count: int) -> None:
    execute('UPDATE chat_unread SET count=%s WHERE id=%s', (count, row_id))


def create_unread_row(user_id: int, conv_id: int, count: int) -> dict | None:
    return insert_returning('chat_unread', {'user_id': user_id, 'conversation_id': conv_id, 'count': count})


# ─────────────────────────────────────────────
# Connections (friend requests)
# ─────────────────────────────────────────────

def get_connection_between(uid1: int, uid2: int) -> dict | None:
    rows = fetch_all(
        'SELECT id,status FROM chat_connections '
        'WHERE (requester_id=%s AND recipient_id=%s) OR (requester_id=%s AND recipient_id=%s)',
        (uid1, uid2, uid2, uid1),
    )
    return rows[0] if rows else None


def get_connections_for_users(uid: int, other_ids: list) -> list:
    return fetch_all(
        'SELECT requester_id,recipient_id,status FROM chat_connections '
        'WHERE (requester_id=%s AND recipient_id = ANY(%s)) OR (requester_id = ANY(%s) AND recipient_id=%s)',
        (uid, other_ids, other_ids, uid),
    )


def get_connection_by_id(conn_id: int) -> dict | None:
    return fetch_one('SELECT * FROM chat_connections WHERE id=%s', (conn_id,))


def reactivate_connection(conn_id: int, requester_id: int, recipient_id: int, updated_at_iso: str) -> None:
    execute(
        'UPDATE chat_connections SET status=%s, requester_id=%s, recipient_id=%s, updated_at=%s WHERE id=%s',
        ('pending', requester_id, recipient_id, updated_at_iso, conn_id),
    )


def create_connection(requester_id: int, recipient_id: int) -> dict | None:
    return insert_returning('chat_connections', {'requester_id': requester_id, 'recipient_id': recipient_id, 'status': 'pending'})


def update_connection_status(conn_id: int, status: str, updated_at_iso: str) -> None:
    execute('UPDATE chat_connections SET status=%s, updated_at=%s WHERE id=%s', (status, updated_at_iso, conn_id))


def delete_connection(conn_id: int) -> None:
    execute('DELETE FROM chat_connections WHERE id=%s', (conn_id,))


def get_pending_requests_for(user_id: int) -> list:
    return fetch_all(
        'SELECT id,requester_id,status,created_at FROM chat_connections WHERE recipient_id=%s AND status=%s',
        (user_id, 'pending'),
    )


def count_pending_requests(user_id: int) -> int:
    row = fetch_one('SELECT COUNT(*) AS count FROM chat_connections WHERE recipient_id=%s AND status=%s', (user_id, 'pending'))
    return row['count'] if row else 0


# ─────────────────────────────────────────────
# User search
# ─────────────────────────────────────────────

def search_users(term: str, exclude_uid: int, limit: int = 10) -> list:
    return fetch_all(
        'SELECT id,username,full_name FROM users WHERE username ILIKE %s AND id != %s LIMIT %s',
        (f'%{term}%', exclude_uid, limit),
    )
